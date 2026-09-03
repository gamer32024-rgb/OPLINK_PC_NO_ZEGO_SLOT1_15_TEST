from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
from pathlib import Path
import random
import threading
import time
from typing import Any, Callable

from .slot_limits import MAX_SLOT, MIN_SLOT


TOUCH_LOGICAL_MAX = 32767
DEFAULT_MIN_SLOT_INTERVAL_MS = 100
DEFAULT_GLOBAL_GESTURE_INTERVAL_MS = 100
DEFAULT_GLOBAL_CLICK_INTERVAL_MS = 100
DEFAULT_CROSS_SLOT_SAME_COORDINATE_MIN_MS = 100
DEFAULT_CROSS_SLOT_SAME_COORDINATE_MAX_MS = 100
DEFAULT_LIVE_RESUME_INTERVAL_MS = 50
DEFAULT_SCRIPT_POST_HID_GUARD_MS = 0
HARD_LAYOUT_POLICY_ID = "starcg_4k_stacked_720p_pico_v2"
PREVIOUS_HARD_LAYOUT_POLICY_ID = "starcg_4k_stacked_720p_pico_v1"
LEGACY_LAYOUT_POLICY_ID = "starcg_4k_4x4_pico_v1"
SUPPORTED_LAYOUT_POLICY_IDS = {
    HARD_LAYOUT_POLICY_ID,
    PREVIOUS_HARD_LAYOUT_POLICY_ID,
    LEGACY_LAYOUT_POLICY_ID,
}
PICO_REPORT_MODE_TOUCHSCREEN = "touchscreen"
PICO_REPORT_MODE_ABSOLUTE_MOUSE = "absolute_mouse"
HARD_LAYOUT_FORMAT = "gui_test_pc_window_layout_v3_hard_4k_stacked"
LEGACY_LAYOUT_FORMAT = "gui_test_pc_window_layout_v2_hard_4k"
HARD_LAYOUT_MODE = "stacked"
HARD_DISPLAY_WIDTH = 3840
HARD_DISPLAY_HEIGHT = 2160
HARD_GRID_COLUMNS = 1
HARD_GRID_ROWS = MAX_SLOT
PREVIOUS_HARD_GRID_ROWS = 15
HARD_GRID_GAP_X = 0
HARD_GRID_GAP_Y = 0
HARD_CLIENT_WIDTH = 1280
HARD_CLIENT_HEIGHT = 720
HARD_OUTER_WIDTH = 1302
HARD_OUTER_HEIGHT = 776
LAYOUT_TOLERANCE_PX = 2

SW_RESTORE = 9
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class PicoTouchError(RuntimeError):
    """Raised when the single-contact Pico transport cannot safely continue."""


class PicoTouchCancelled(PicoTouchError):
    """Raised when one slot is cancelled before its queued touch can start."""


@dataclass(frozen=True)
class _GestureWaiter:
    ticket: int
    slot: int
    x: int
    y: int
    gesture_kind: str
    ready_at: float


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


@dataclass(frozen=True)
class TouchSurface:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TouchSurface":
        surface = cls(
            left=int(value.get("left", 0)),
            top=int(value.get("top", 0)),
            width=int(value["width"]),
            height=int(value["height"]),
        )
        if surface.width < 2 or surface.height < 2:
            raise PicoTouchError("touch_surface width and height must both be at least 2")
        return surface

    def map_screen_point(self, x: int, y: int) -> tuple[int, int]:
        right = self.left + self.width
        bottom = self.top + self.height
        if not (self.left <= x < right and self.top <= y < bottom):
            raise PicoTouchError(
                f"screen point {x},{y} is outside configured touch surface "
                f"{self.left},{self.top} {self.width}x{self.height}"
            )
        logical_x = round((x - self.left) * TOUCH_LOGICAL_MAX / (self.width - 1))
        logical_y = round((y - self.top) * TOUCH_LOGICAL_MAX / (self.height - 1))
        return int(logical_x), int(logical_y)


@dataclass(frozen=True)
class PicoLayoutPolicy:
    policy_id: str
    layout_mode: str
    display: TouchSurface
    columns: int
    rows: int
    origin_x: int
    origin_y: int
    gap_x: int
    gap_y: int
    client_width: int
    client_height: int
    outer_width: int
    outer_height: int

    @classmethod
    def from_file(cls, path: str | Path) -> "PicoLayoutPolicy":
        source = Path(path)
        if not source.is_file():
            raise PicoTouchError(f"hard 4K layout config was not found: {source}")
        try:
            value = json.loads(source.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise PicoTouchError(f"invalid hard 4K layout JSON: {source}: {exc}") from exc
        if not isinstance(value, dict):
            raise PicoTouchError("hard 4K layout config must be a JSON object")
        display = value.get("display")
        grid = value.get("grid")
        expected_client = value.get("expected_client")
        expected_outer = value.get("expected_outer")
        if not all(isinstance(item, dict) for item in (display, grid, expected_client, expected_outer)):
            raise PicoTouchError("hard 4K layout config is missing display/grid/window geometry")

        policy = cls(
            policy_id=str(value.get("policy_id") or ""),
            layout_mode=str(value.get("layout_mode") or "grid"),
            display=TouchSurface.from_dict(display),
            columns=int(grid.get("columns", 0)),
            rows=int(grid.get("rows", 0)),
            origin_x=int(grid.get("origin_x", 0)),
            origin_y=int(grid.get("origin_y", 0)),
            gap_x=int(grid.get("gap_x", 0)),
            gap_y=int(grid.get("gap_y", 0)),
            client_width=int(expected_client.get("width", 0)),
            client_height=int(expected_client.get("height", 0)),
            outer_width=int(expected_outer.get("width", 0)),
            outer_height=int(expected_outer.get("height", 0)),
        )
        try:
            expected = cls.hardcoded(policy.policy_id)
        except PicoTouchError as exc:
            raise PicoTouchError(
                f"unsupported locked layout policy {policy.policy_id!r}; Pico output is blocked"
            ) from exc
        expected_format = (
            HARD_LAYOUT_FORMAT
            if policy.policy_id in {HARD_LAYOUT_POLICY_ID, PREVIOUS_HARD_LAYOUT_POLICY_ID}
            else LEGACY_LAYOUT_FORMAT
        )
        if value.get("format") != expected_format or value.get("locked") is not True or policy != expected:
            raise PicoTouchError(
                f"layout config does not match locked policy {policy.policy_id}; Pico output is blocked"
            )
        return policy

    @classmethod
    def hardcoded(cls, policy_id: str = HARD_LAYOUT_POLICY_ID) -> "PicoLayoutPolicy":
        if policy_id == LEGACY_LAYOUT_POLICY_ID:
            return cls(
                policy_id=LEGACY_LAYOUT_POLICY_ID,
                layout_mode="grid",
                display=TouchSurface(0, 0, HARD_DISPLAY_WIDTH, HARD_DISPLAY_HEIGHT),
                columns=4,
                rows=4,
                origin_x=0,
                origin_y=0,
                gap_x=12,
                gap_y=12,
                client_width=768,
                client_height=432,
                outer_width=790,
                outer_height=488,
            )
        if policy_id not in {HARD_LAYOUT_POLICY_ID, PREVIOUS_HARD_LAYOUT_POLICY_ID}:
            raise PicoTouchError(f"unknown locked layout policy: {policy_id}")
        return cls(
            policy_id=policy_id,
            layout_mode=HARD_LAYOUT_MODE,
            display=TouchSurface(0, 0, HARD_DISPLAY_WIDTH, HARD_DISPLAY_HEIGHT),
            columns=HARD_GRID_COLUMNS,
            rows=(
                HARD_GRID_ROWS
                if policy_id == HARD_LAYOUT_POLICY_ID
                else PREVIOUS_HARD_GRID_ROWS
            ),
            origin_x=0,
            origin_y=0,
            gap_x=HARD_GRID_GAP_X,
            gap_y=HARD_GRID_GAP_Y,
            client_width=HARD_CLIENT_WIDTH,
            client_height=HARD_CLIENT_HEIGHT,
            outer_width=HARD_OUTER_WIDTH,
            outer_height=HARD_OUTER_HEIGHT,
        )

    def target_outer(self, slot: int) -> tuple[int, int]:
        policy_max_slot = self.rows if self.layout_mode == "stacked" else self.columns * self.rows
        policy_max_slot = min(MAX_SLOT, policy_max_slot)
        if slot < MIN_SLOT or slot > policy_max_slot:
            raise PicoTouchError(
                f"hard 4K layout slot must be {MIN_SLOT}-{policy_max_slot}, got {slot}"
            )
        if self.layout_mode == "stacked":
            return self.origin_x, self.origin_y
        index = int(slot) - 1
        column = index % self.columns
        row = index // self.columns
        return (
            self.origin_x + column * (self.outer_width + self.gap_x),
            self.origin_y + row * (self.outer_height + self.gap_y),
        )


@dataclass(frozen=True)
class PicoTouchConfig:
    enabled: bool
    port: str
    touch_surface: TouchSurface
    coordinate_policy: str
    report_mode: str = PICO_REPORT_MODE_TOUCHSCREEN
    min_slot_interval_ms: int = DEFAULT_MIN_SLOT_INTERVAL_MS
    global_gesture_interval_ms: int = DEFAULT_GLOBAL_GESTURE_INTERVAL_MS
    global_click_interval_ms: int = DEFAULT_GLOBAL_CLICK_INTERVAL_MS
    cross_slot_same_coordinate_min_ms: int = DEFAULT_CROSS_SLOT_SAME_COORDINATE_MIN_MS
    cross_slot_same_coordinate_max_ms: int = DEFAULT_CROSS_SLOT_SAME_COORDINATE_MAX_MS
    tap_hold_ms: int = 60
    ack_timeout_ms: int = 1000
    foreground_settle_ms: int = 80
    script_post_hid_guard_ms: int = DEFAULT_SCRIPT_POST_HID_GUARD_MS
    move_min_interval_ms: int = 8
    drag_release_guard_ms: int = 250
    hid_drain_timeout_ms: int = 1500
    live_resume_interval_ms: int = DEFAULT_LIVE_RESUME_INTERVAL_MS
    allow_unfocused_visible_target: bool = False

    @classmethod
    def from_file(cls, path: str | Path) -> "PicoTouchConfig":
        source = Path(path)
        if not source.is_file():
            raise PicoTouchError(f"Pico touch config was not found: {source}")
        try:
            value = json.loads(source.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise PicoTouchError(f"invalid Pico touch config JSON: {source}: {exc}") from exc
        if not isinstance(value, dict):
            raise PicoTouchError("Pico touch config must be a JSON object")

        port = str(value.get("port") or "").strip()
        if not port:
            raise PicoTouchError("Pico touch config requires an explicit COM port")
        surface_value = value.get("touch_surface")
        if not isinstance(surface_value, dict):
            raise PicoTouchError("Pico touch config requires touch_surface")

        config = cls(
            enabled=bool(value.get("enabled", False)),
            port=port,
            touch_surface=TouchSurface.from_dict(surface_value),
            coordinate_policy=str(value.get("coordinate_policy") or ""),
            report_mode=str(
                value.get("report_mode") or PICO_REPORT_MODE_TOUCHSCREEN
            ).strip().casefold(),
            min_slot_interval_ms=int(value.get("min_slot_interval_ms", DEFAULT_MIN_SLOT_INTERVAL_MS)),
            global_gesture_interval_ms=int(
                value.get("global_gesture_interval_ms", DEFAULT_GLOBAL_GESTURE_INTERVAL_MS)
            ),
            global_click_interval_ms=int(
                value.get("global_click_interval_ms", DEFAULT_GLOBAL_CLICK_INTERVAL_MS)
            ),
            cross_slot_same_coordinate_min_ms=int(
                value.get(
                    "cross_slot_same_coordinate_min_ms",
                    DEFAULT_CROSS_SLOT_SAME_COORDINATE_MIN_MS,
                )
            ),
            cross_slot_same_coordinate_max_ms=int(
                value.get(
                    "cross_slot_same_coordinate_max_ms",
                    DEFAULT_CROSS_SLOT_SAME_COORDINATE_MAX_MS,
                )
            ),
            tap_hold_ms=int(value.get("tap_hold_ms", 60)),
            ack_timeout_ms=int(value.get("ack_timeout_ms", 1000)),
            foreground_settle_ms=int(value.get("foreground_settle_ms", 80)),
            script_post_hid_guard_ms=int(
                value.get(
                    "script_post_hid_guard_ms",
                    DEFAULT_SCRIPT_POST_HID_GUARD_MS,
                )
            ),
            move_min_interval_ms=int(value.get("move_min_interval_ms", 8)),
            drag_release_guard_ms=int(value.get("drag_release_guard_ms", 250)),
            hid_drain_timeout_ms=int(value.get("hid_drain_timeout_ms", 1500)),
            live_resume_interval_ms=int(
                value.get("live_resume_interval_ms", DEFAULT_LIVE_RESUME_INTERVAL_MS)
            ),
            allow_unfocused_visible_target=bool(value.get("allow_unfocused_visible_target", False)),
        )
        if not config.enabled:
            raise PicoTouchError("Pico touch is disabled; set enabled=true only after the zero-touch serial smoke test")
        if config.coordinate_policy not in SUPPORTED_LAYOUT_POLICY_IDS:
            raise PicoTouchError(
                "Pico coordinate_policy is not a supported locked layout policy; HID output is blocked"
            )
        if config.report_mode != PICO_REPORT_MODE_TOUCHSCREEN:
            raise PicoTouchError(
                "GUI_TEST_PC requires Pico report_mode=touchscreen; mouse HID output is blocked"
            )
        if config.touch_surface != PicoLayoutPolicy.hardcoded(config.coordinate_policy).display:
            raise PicoTouchError(
                f"Pico touch_surface must be 0,0 {HARD_DISPLAY_WIDTH}x{HARD_DISPLAY_HEIGHT}; HID output is blocked"
            )
        if config.min_slot_interval_ms < DEFAULT_MIN_SLOT_INTERVAL_MS:
            raise PicoTouchError(
                f"min_slot_interval_ms must be at least {DEFAULT_MIN_SLOT_INTERVAL_MS}"
            )
        if config.global_gesture_interval_ms < DEFAULT_GLOBAL_GESTURE_INTERVAL_MS:
            raise PicoTouchError(
                "global_gesture_interval_ms must be at least "
                f"{DEFAULT_GLOBAL_GESTURE_INTERVAL_MS}"
            )
        if config.global_click_interval_ms < DEFAULT_GLOBAL_CLICK_INTERVAL_MS:
            raise PicoTouchError(
                f"global_click_interval_ms must be at least {DEFAULT_GLOBAL_CLICK_INTERVAL_MS}"
            )
        if (
            config.cross_slot_same_coordinate_min_ms < config.global_click_interval_ms
            or config.cross_slot_same_coordinate_max_ms
            < config.cross_slot_same_coordinate_min_ms
        ):
            raise PicoTouchError(
                "cross-slot same-coordinate timing must be ordered after global_click_interval_ms"
            )
        if (
            config.tap_hold_ms < 1
            or config.ack_timeout_ms < 100
            or not 0 <= config.script_post_hid_guard_ms <= 5000
            or config.move_min_interval_ms < 0
            or config.drag_release_guard_ms < 0
            or config.hid_drain_timeout_ms < 100
            or not 0 <= config.live_resume_interval_ms <= 1000
        ):
            raise PicoTouchError("invalid Pico touch timing values")
        return config


def _state_int(status: str, name: str) -> int:
    prefix = f"{name}="
    for field in str(status).split():
        if field.startswith(prefix):
            try:
                return int(field[len(prefix):])
            except ValueError as exc:
                raise PicoTouchError(f"invalid Pico STATE field {field!r}") from exc
    raise PicoTouchError(f"Pico STATE response is missing {name}: {status}")


def _protocol_command(report_mode: str, touch_command: str) -> str:
    if report_mode == PICO_REPORT_MODE_TOUCHSCREEN:
        return touch_command
    if report_mode == PICO_REPORT_MODE_ABSOLUTE_MOUSE:
        return {
            "DOWN": "MDOWN",
            "MOVE": "MMOVE",
            "UP": "MUP",
        }[touch_command]
    raise PicoTouchError(f"unsupported Pico report mode: {report_mode}")


class PicoCdcTransport:
    def __init__(self, config: PicoTouchConfig) -> None:
        self._config = config
        self._serial: Any | None = None
        self._next_seq = 1

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def ensure_ready(self) -> None:
        if self._serial is None:
            self._open()
        ready = self._write_and_wait("HELLO 1", expected_prefix="READY", include_seq=False)
        if "proto=1" not in ready or "hid=1" not in ready:
            raise PicoTouchError(f"unexpected Pico HELLO response: {ready}")
        status = self.command("STATUS")
        if not status.startswith("STATE ") or "hid=1" not in status:
            raise PicoTouchError(f"unexpected Pico STATUS response: {status}")
        if _state_int(status, self._active_state_field()) != 0 or _state_int(status, "queued") != 0:
            self.command("CANCEL")
            self.wait_hid_idle()

    def wait_hid_idle(self) -> str:
        deadline = time.monotonic() + self._config.hid_drain_timeout_ms / 1000.0
        last_status = ""
        while time.monotonic() < deadline:
            last_status = self.command("STATUS")
            if (
                _state_int(last_status, self._active_state_field()) == 0
                and _state_int(last_status, "queued") == 0
            ):
                return last_status
            time.sleep(0.005)
        raise PicoTouchError(
            f"Pico HID queue did not drain within {self._config.hid_drain_timeout_ms}ms: {last_status}"
        )

    def _active_state_field(self) -> str:
        if self._config.report_mode == PICO_REPORT_MODE_ABSOLUTE_MOUSE:
            return "mouse"
        return "tip"

    def command(self, name: str, *args: int) -> str:
        if self._serial is None:
            self.ensure_ready()
        sequence = self._next_seq
        self._next_seq += 1
        command = " ".join([name, str(sequence), *(str(int(arg)) for arg in args)])
        return self._write_and_wait(command, expected_prefix=("ACK", "STATE", "ERR"), expected_seq=sequence)

    def _open(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise PicoTouchError("pyserial is required for the Pico HID touch backend") from exc

        try:
            port = serial.Serial(
                port=self._config.port,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=self._config.ack_timeout_ms / 1000.0,
            )
            port.dtr = True
            port.rts = True
            port.reset_input_buffer()
        except Exception as exc:
            raise PicoTouchError(f"could not open Pico serial port {self._config.port}: {exc}") from exc
        self._serial = port

    def _write_and_wait(
        self,
        command: str,
        *,
        expected_prefix: str | tuple[str, ...],
        expected_seq: int | None = None,
        include_seq: bool = True,
    ) -> str:
        if self._serial is None:
            raise PicoTouchError("Pico serial port is not open")
        try:
            self._serial.write((command + "\n").encode("ascii"))
            self._serial.flush()
        except Exception as exc:
            self.close()
            raise PicoTouchError(f"Pico serial write failed: {exc}") from exc

        prefixes = (expected_prefix,) if isinstance(expected_prefix, str) else expected_prefix
        deadline = time.monotonic() + self._config.ack_timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                raw = self._serial.readline()
            except Exception as exc:
                self.close()
                raise PicoTouchError(f"Pico serial read failed: {exc}") from exc
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            if expected_seq is not None and line.startswith(("ACK ", "STATE ", "ERR ")):
                fields = line.split(maxsplit=2)
                if len(fields) < 2 or fields[1] != str(expected_seq):
                    continue
            if line.startswith(prefixes):
                if line.startswith("ERR "):
                    raise PicoTouchError(f"Pico rejected {command!r}: {line}")
                return line

        suffix = f" sequence {expected_seq}" if include_seq and expected_seq is not None else ""
        raise PicoTouchError(f"timed out waiting for Pico response to {command!r}{suffix}")


class PicoTouchScheduler:
    """Serializes one global touch contact while preserving each slot's cooldown."""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        self.layout_config_path = self.config_path.with_name("window_layout.json")
        self.config = PicoTouchConfig.from_file(self.config_path)
        self.layout_policy = PicoLayoutPolicy.from_file(self.layout_config_path)
        if self.config.coordinate_policy != self.layout_policy.policy_id:
            raise PicoTouchError(
                "Pico coordinate policy does not match the active window layout; HID output is blocked"
            )
        self.transport = PicoCdcTransport(self.config)
        self._gesture_lock = threading.Lock()
        self._gesture_wait_condition = threading.Condition()
        self._gesture_waiters: list[_GestureWaiter] = []
        self._next_gesture_ticket = 1
        self._slot_locks: dict[int, threading.Lock] = {}
        self._slot_locks_guard = threading.Lock()
        self._next_allowed: dict[int, float] = {}
        self._next_global_allowed = 0.0
        self._next_click_allowed = 0.0
        self._coordinate_click_deadlines: dict[tuple[int, int], tuple[int, float]] = {}
        self._active_slot: int | None = None
        self._active_slot_lock: threading.Lock | None = None
        self._active_hwnd: int | None = None
        self._active_policy: PicoLayoutPolicy | None = None
        self._active_gesture_kind = "contact"
        self._active_click_point: tuple[int, int] | None = None
        self._last_client_point = (0, 0)
        self._last_reported_client_point = (0, 0)
        self._active_moved = False
        self._last_move_at = 0.0
        self._activity_lock = threading.Lock()
        self._activity_active_slot: int | None = None
        self._last_hid_activity_slot: int | None = None
        self._last_hid_activity_at = 0.0
        self._user32 = _foreground_api()
        self._kernel32 = _kernel32_api()

    def ensure_ready(self) -> None:
        with self._gesture_lock:
            self._validate_hard_environment()
            self.transport.ensure_ready()

    def health_check(self) -> str:
        """Verify the CDC control channel without sending game input."""
        with self._gesture_lock:
            self._validate_hard_environment()
            self.transport.ensure_ready()
            return self.transport.command("STATUS")

    def activity_snapshot(self, hold_seconds: float = 1.0) -> dict[str, object]:
        """Return the single Slot most recently acknowledged by Pico HID."""
        now = time.monotonic()
        with self._activity_lock:
            active_slot = self._activity_active_slot
            last_slot = self._last_hid_activity_slot
            last_at = self._last_hid_activity_at
        age_seconds = max(0.0, now - last_at) if last_at > 0 else None
        visible_slot = active_slot
        if visible_slot is None and last_slot is not None and age_seconds is not None:
            if age_seconds <= max(0.0, float(hold_seconds)):
                visible_slot = last_slot
        return {
            "slot": visible_slot,
            "contact_active": active_slot is not None,
            "last_slot": last_slot,
            "age_ms": None if age_seconds is None else int(age_seconds * 1000),
            "minimum_hold_ms": int(max(0.0, float(hold_seconds)) * 1000),
        }

    def _record_hid_activity(self, slot: int, *, contact_active: bool) -> None:
        with self._activity_lock:
            self._last_hid_activity_slot = int(slot)
            self._last_hid_activity_at = time.monotonic()
            self._activity_active_slot = int(slot) if contact_active else None

    def click(
        self,
        slot: int,
        hwnd: int,
        x: int,
        y: int,
        duration_sec: float,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> float:
        schedule_delay = self.begin(
            slot,
            hwnd,
            x,
            y,
            gesture_kind="click",
            stop_requested=stop_requested,
        )
        try:
            if not _sleep_interruptibly(
                max(float(duration_sec), self.config.tap_hold_ms / 1000.0),
                stop_requested,
            ):
                raise PicoTouchCancelled(f"Pico playback cancelled for slot {slot}")
        finally:
            self.end(x, y)
        return schedule_delay

    def begin(
        self,
        slot: int,
        hwnd: int,
        x: int,
        y: int,
        *,
        gesture_kind: str = "contact",
        bypass_slot_cooldown: bool = False,
        bypass_slot_lock: bool = False,
        stop_requested: Callable[[], bool] | None = None,
    ) -> float:
        requested_at = time.monotonic()
        slot_lock = None if bypass_slot_lock else self._slot_lock(slot)
        if slot_lock is not None and not _acquire_interruptibly(slot_lock, stop_requested):
            raise PicoTouchCancelled(f"Pico playback cancelled while slot {slot} was queued")
        gesture_acquired = False
        try:
            if not bypass_slot_cooldown and not self._wait_for_slot(
                slot,
                stop_requested=stop_requested,
            ):
                raise PicoTouchCancelled(f"Pico playback cancelled during slot {slot} cooldown")
            if not self._acquire_gesture_turn(
                slot=slot,
                x=x,
                y=y,
                gesture_kind=gesture_kind,
                stop_requested=stop_requested,
            ):
                raise PicoTouchCancelled(
                    f"Pico playback cancelled while slot {slot} was waiting for HID"
                )
            gesture_acquired = True
            if self._active_slot is not None:
                raise PicoTouchError("Pico gesture lock acquired while another touch contact is active")
            if _call_stop_requested(stop_requested):
                raise PicoTouchCancelled(f"Pico playback cancelled before slot {slot} touch DOWN")
            self.transport.ensure_ready()
            foreground_changed = self._activate_target_window(hwnd)
            if foreground_changed and self.config.foreground_settle_ms and not _sleep_interruptibly(
                self.config.foreground_settle_ms / 1000.0,
                stop_requested,
            ):
                raise PicoTouchCancelled(f"Pico playback cancelled while slot {slot} was activating")
            policy = self._validate_hard_environment()
            self._ensure_slot_window_geometry(policy, slot, hwnd)
            screen_x, screen_y = self._validated_client_to_screen(policy, slot, hwnd, x, y)
            self._verify_target_foreground(hwnd, screen_x, screen_y)
            logical_x, logical_y = self.config.touch_surface.map_screen_point(screen_x, screen_y)
            command = _protocol_command(self.config.report_mode, "DOWN")
            response = self.transport.command(command, logical_x, logical_y)
            if f"{command} queued=1" not in response:
                raise PicoTouchError(f"unexpected Pico {command} response: {response}")
            self._active_slot = int(slot)
            self._active_slot_lock = slot_lock
            self._active_hwnd = int(hwnd)
            self._active_policy = policy
            self._active_gesture_kind = str(gesture_kind)
            self._active_click_point = (int(x), int(y)) if gesture_kind == "click" else None
            self._last_client_point = (int(x), int(y))
            self._last_reported_client_point = (int(x), int(y))
            self._active_moved = False
            self._last_move_at = time.monotonic()
            self._record_hid_activity(slot, contact_active=True)
            return max(0.0, self._last_move_at - requested_at)
        except Exception:
            if gesture_acquired:
                self._gesture_lock.release()
                self._notify_gesture_waiters()
            if slot_lock is not None:
                slot_lock.release()
            raise

    def begin_live(
        self,
        slot: int,
        hwnd: int,
        x: int,
        y: int,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> float:
        """Start a human-driven contact without script anti-repeat cooldowns."""
        return self.begin(
            slot,
            hwnd,
            x,
            y,
            gesture_kind="live",
            bypass_slot_cooldown=True,
            bypass_slot_lock=True,
            stop_requested=stop_requested,
        )

    def move(self, x: int, y: int, *, force: bool = False) -> None:
        if self._active_slot is None or self._active_hwnd is None:
            raise PicoTouchError("cannot MOVE without an active Pico touch contact")
        now = time.monotonic()
        requested_point = (int(x), int(y))
        self._last_client_point = requested_point
        if not force and (now - self._last_move_at) * 1000 < self.config.move_min_interval_ms:
            return
        policy = self._active_policy
        if policy is None:
            raise PicoTouchError("locked 4K layout policy is missing during active Pico gesture")
        screen_x, screen_y = self._validated_client_to_screen(
            policy,
            self._active_slot,
            self._active_hwnd,
            x,
            y,
        )
        logical_x, logical_y = self.config.touch_surface.map_screen_point(screen_x, screen_y)
        command = _protocol_command(self.config.report_mode, "MOVE")
        response = self.transport.command(command, logical_x, logical_y)
        if f"{command} queued=1" not in response:
            raise PicoTouchError(f"unexpected Pico {command} response: {response}")
        if requested_point != self._last_reported_client_point:
            self._active_moved = True
        self._last_reported_client_point = requested_point
        self._last_move_at = now
        self._record_hid_activity(self._active_slot, contact_active=True)

    def end(self, x: int | None = None, y: int | None = None) -> None:
        if self._active_slot is None:
            return
        slot = self._active_slot
        try:
            if x is not None and y is not None and (int(x), int(y)) != self._last_reported_client_point:
                self.move(x, y, force=True)
            if self._active_moved and self.config.drag_release_guard_ms:
                remaining = (
                    self.config.drag_release_guard_ms / 1000.0
                    - (time.monotonic() - self._last_move_at)
                )
                if remaining > 0:
                    time.sleep(remaining)
            command = _protocol_command(self.config.report_mode, "UP")
            response = self.transport.command(command)
            if f"{command} queued=1" not in response:
                raise PicoTouchError(f"unexpected Pico {command} response: {response}")
            self.transport.wait_hid_idle()
            self._hold_script_target_after_hid()
        except Exception:
            self._cancel_transport()
            raise
        finally:
            completed_at = time.monotonic()
            self._record_hid_activity(slot, contact_active=False)
            self._record_completed_gesture(slot, completed_at)
            self._release_active()

    def _hold_script_target_after_hid(self) -> None:
        if self._active_gesture_kind == "live":
            return
        guard_ms = int(self.config.script_post_hid_guard_ms)
        if guard_ms > 0:
            time.sleep(guard_ms / 1000.0)

    def cancel(self, *, expected_slot: int | None = None) -> None:
        if expected_slot is not None and self._active_slot != int(expected_slot):
            return
        try:
            self._cancel_transport()
        finally:
            if self._active_slot is not None:
                slot = self._active_slot
                completed_at = time.monotonic()
                self._record_hid_activity(slot, contact_active=False)
                self._record_completed_gesture(slot, completed_at)
                self._release_active()

    def _record_completed_gesture(self, slot: int, completed_at: float) -> None:
        if self._active_gesture_kind == "live":
            self._next_global_allowed = (
                completed_at + self.config.live_resume_interval_ms / 1000.0
            )
            self._next_allowed[int(slot)] = (
                completed_at + self.config.min_slot_interval_ms / 1000.0
            )
            return
        self._next_global_allowed = (
            completed_at + self.config.global_gesture_interval_ms / 1000.0
        )
        self._next_allowed[int(slot)] = (
            completed_at + self.config.min_slot_interval_ms / 1000.0
        )
        if self._active_gesture_kind != "click" or self._active_click_point is None:
            return
        self._next_click_allowed = (
            completed_at + self.config.global_click_interval_ms / 1000.0
        )
        same_coordinate_delay = random.uniform(
            self.config.cross_slot_same_coordinate_min_ms,
            self.config.cross_slot_same_coordinate_max_ms,
        ) / 1000.0
        self._coordinate_click_deadlines[self._active_click_point] = (
            int(slot),
            completed_at + same_coordinate_delay,
        )

    def _cancel_transport(self) -> None:
        try:
            if self.transport._serial is not None:
                self.transport.command("CANCEL")
                self.transport.wait_hid_idle()
        except PicoTouchError:
            self.transport.close()

    def _release_active(self) -> None:
        slot_lock = self._active_slot_lock
        self._active_slot = None
        self._active_slot_lock = None
        self._active_hwnd = None
        self._active_policy = None
        self._active_gesture_kind = "contact"
        self._active_click_point = None
        self._active_moved = False
        self._gesture_lock.release()
        self._notify_gesture_waiters()
        if slot_lock is not None:
            slot_lock.release()

    def _slot_lock(self, slot: int) -> threading.Lock:
        if slot < MIN_SLOT or slot > MAX_SLOT:
            raise PicoTouchError(
                f"Pico touch slot must be {MIN_SLOT}-{MAX_SLOT}, got {slot}"
            )
        with self._slot_locks_guard:
            return self._slot_locks.setdefault(int(slot), threading.Lock())

    def _wait_for_slot(
        self,
        slot: int,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        delay = self._next_allowed.get(slot, 0.0) - time.monotonic()
        return _sleep_interruptibly(max(0.0, delay), stop_requested)

    def _acquire_gesture_turn(
        self,
        *,
        slot: int,
        x: int,
        y: int,
        gesture_kind: str,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        with self._gesture_wait_condition:
            waiter = _GestureWaiter(
                ticket=self._next_gesture_ticket,
                slot=int(slot),
                x=int(x),
                y=int(y),
                gesture_kind=str(gesture_kind),
                ready_at=time.monotonic(),
            )
            self._next_gesture_ticket += 1
            self._gesture_waiters.append(waiter)
            self._gesture_wait_condition.notify_all()

        acquired = False
        try:
            while True:
                if _call_stop_requested(stop_requested):
                    return False
                with self._gesture_wait_condition:
                    now = time.monotonic()
                    winner = self._ready_gesture_waiter(now)
                    if winner == waiter and self._gesture_lock.acquire(blocking=False):
                        self._gesture_waiters.remove(waiter)
                        acquired = True
                        self._gesture_wait_condition.notify_all()
                        return True

                    deadline = self._gesture_deadline(
                        slot=waiter.slot,
                        x=waiter.x,
                        y=waiter.y,
                        gesture_kind=waiter.gesture_kind,
                    )
                    remaining = max(0.0, deadline - now)
                    self._gesture_wait_condition.wait(timeout=min(0.05, remaining or 0.05))
        finally:
            if not acquired:
                with self._gesture_wait_condition:
                    if waiter in self._gesture_waiters:
                        self._gesture_waiters.remove(waiter)
                        self._gesture_wait_condition.notify_all()

    def _ready_gesture_waiter(self, now: float) -> _GestureWaiter | None:
        ready = [
            waiter
            for waiter in self._gesture_waiters
            if self._gesture_deadline(
                slot=waiter.slot,
                x=waiter.x,
                y=waiter.y,
                gesture_kind=waiter.gesture_kind,
            )
            <= now
        ]
        if not ready:
            return None
        live_ready = [waiter for waiter in ready if waiter.gesture_kind == "live"]
        if live_ready:
            return min(live_ready, key=lambda waiter: waiter.ticket)
        oldest_ready_at = min(waiter.ready_at for waiter in ready)
        simultaneous = [
            waiter for waiter in ready if waiter.ready_at <= oldest_ready_at + 0.05
        ]
        return min(simultaneous, key=lambda waiter: (waiter.slot, waiter.ticket))

    def _notify_gesture_waiters(self) -> None:
        with self._gesture_wait_condition:
            self._gesture_wait_condition.notify_all()

    def _gesture_deadline(
        self,
        *,
        slot: int,
        x: int,
        y: int,
        gesture_kind: str,
    ) -> float:
        if gesture_kind == "live":
            return 0.0
        deadline = self._next_global_allowed
        if gesture_kind != "click":
            return deadline
        deadline = max(deadline, self._next_click_allowed)
        previous = self._coordinate_click_deadlines.get((int(x), int(y)))
        if previous is not None and int(previous[0]) != int(slot):
            deadline = max(deadline, float(previous[1]))
        return deadline

    def _verify_target_foreground(self, hwnd: int, screen_x: int, screen_y: int) -> None:
        foreground = int(self._user32.GetForegroundWindow() or 0)
        if foreground == int(hwnd):
            return
        if self.config.allow_unfocused_visible_target and self._point_targets_window(hwnd, screen_x, screen_y):
            return
        if self.config.allow_unfocused_visible_target:
            raise PicoTouchError(
                f"target is unfocused and screen point {screen_x},{screen_y} is covered by another window"
            )
        raise PicoTouchError(
            f"target window did not become foreground: expected=0x{int(hwnd):X} actual=0x{foreground:X}"
        )

    def _validate_hard_environment(
        self,
        *,
        slot: int | None = None,
        hwnd: int | None = None,
    ) -> PicoLayoutPolicy:
        self._force_physical_dpi_context()
        current_config = PicoTouchConfig.from_file(self.config_path)
        if current_config != self.config:
            raise PicoTouchError("Pico config changed after startup; restart GUI_TEST_PC before HID output")
        # The scheduler uses the policy validated at startup. A partial on-disk
        # deployment cannot disable an otherwise safe live touch session.
        policy = self.layout_policy
        desktop = self._physical_desktop()
        if desktop != policy.display:
            raise PicoTouchError(
                "hard 4K layout blocked: current physical desktop is "
                f"{desktop.left},{desktop.top} {desktop.width}x{desktop.height}, expected "
                f"{policy.display.left},{policy.display.top} {policy.display.width}x{policy.display.height}"
            )
        if slot is not None or hwnd is not None:
            if slot is None or hwnd is None:
                raise PicoTouchError("slot and hwnd must both be provided for hard layout validation")
            self._validate_slot_window(policy, int(slot), int(hwnd))
        return policy

    def _force_physical_dpi_context(self) -> None:
        ctypes.set_last_error(0)
        previous = self._user32.SetThreadDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
        if not previous:
            raise PicoTouchError(
                f"could not enable physical DPI coordinates: WinError {ctypes.get_last_error()}"
            )

    def _physical_desktop(self) -> TouchSurface:
        return TouchSurface(
            left=int(self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            top=int(self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            width=int(self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            height=int(self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
        )

    def _window_geometry(self, hwnd: int) -> dict[str, int]:
        self._force_physical_dpi_context()
        outer = RECT()
        client = RECT()
        origin = POINT(0, 0)
        handle = wintypes.HWND(int(hwnd))
        if not self._user32.GetWindowRect(handle, ctypes.byref(outer)):
            raise PicoTouchError(f"GetWindowRect failed for hwnd=0x{int(hwnd):X}")
        if not self._user32.GetClientRect(handle, ctypes.byref(client)):
            raise PicoTouchError(f"GetClientRect failed for hwnd=0x{int(hwnd):X}")
        if not self._user32.ClientToScreen(handle, ctypes.byref(origin)):
            raise PicoTouchError(f"ClientToScreen failed for hwnd=0x{int(hwnd):X}")
        return {
            "outer_x": int(outer.left),
            "outer_y": int(outer.top),
            "outer_width": int(outer.right - outer.left),
            "outer_height": int(outer.bottom - outer.top),
            "client_x": int(origin.x),
            "client_y": int(origin.y),
            "client_width": int(client.right - client.left),
            "client_height": int(client.bottom - client.top),
        }

    def _validate_slot_window(
        self,
        policy: PicoLayoutPolicy,
        slot: int,
        hwnd: int,
    ) -> dict[str, int]:
        geometry = self._window_geometry(hwnd)
        target_x, target_y = policy.target_outer(slot)
        expected = {
            "outer_x": target_x,
            "outer_y": target_y,
            "outer_width": policy.outer_width,
            "outer_height": policy.outer_height,
            "client_width": policy.client_width,
            "client_height": policy.client_height,
        }
        mismatches = [
            f"{name}={geometry[name]} expected={value}"
            for name, value in expected.items()
            if abs(geometry[name] - value) > LAYOUT_TOLERANCE_PX
        ]
        if mismatches:
            raise PicoTouchError(
                f"hard 4K layout blocked slot {slot}: "
                + ", ".join(mismatches)
                + "; run Arrange All Windows before Pico playback"
            )
        return geometry

    def _ensure_slot_window_geometry(
        self,
        policy: PicoLayoutPolicy,
        slot: int,
        hwnd: int,
    ) -> dict[str, int]:
        try:
            return self._validate_slot_window(policy, slot, hwnd)
        except PicoTouchError as original_error:
            target_x, target_y = policy.target_outer(slot)
            handle = wintypes.HWND(int(hwnd))
            self._user32.ShowWindow(handle, SW_RESTORE)
            flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER
            ctypes.set_last_error(0)
            moved = self._user32.SetWindowPos(
                handle,
                wintypes.HWND(0),
                int(target_x),
                int(target_y),
                int(policy.outer_width),
                int(policy.outer_height),
                flags,
            )
            if not moved:
                raise PicoTouchError(
                    f"hard 4K layout repair failed slot {slot}: "
                    f"SetWindowPos WinError {ctypes.get_last_error()}"
                ) from original_error
            time.sleep(max(0.05, self.config.foreground_settle_ms / 1000.0))
            try:
                return self._validate_slot_window(policy, slot, hwnd)
            except PicoTouchError as repair_error:
                raise PicoTouchError(
                    f"hard 4K layout repair did not restore slot {slot}: {repair_error}"
                ) from original_error

    def _validated_client_to_screen(
        self,
        policy: PicoLayoutPolicy,
        slot: int,
        hwnd: int,
        x: int,
        y: int,
    ) -> tuple[int, int]:
        geometry = self._validate_slot_window(policy, slot, hwnd)
        if not (0 <= int(x) < policy.client_width and 0 <= int(y) < policy.client_height):
            raise PicoTouchError(
                f"hard 4K layout blocked slot {slot}: client point {x},{y} is outside "
                f"{policy.client_width}x{policy.client_height}"
            )
        screen_x, screen_y = self._client_to_screen(hwnd, x, y)
        expected_x = geometry["client_x"] + int(x)
        expected_y = geometry["client_y"] + int(y)
        if screen_x != expected_x or screen_y != expected_y:
            raise PicoTouchError(
                f"hard 4K layout blocked slot {slot}: DPI coordinate disagreement "
                f"actual={screen_x},{screen_y} expected={expected_x},{expected_y}"
            )
        self.config.touch_surface.map_screen_point(screen_x, screen_y)
        return screen_x, screen_y

    def _activate_target_window(self, hwnd: int) -> bool:
        hwnd_handle = wintypes.HWND(int(hwnd))
        if int(self._user32.GetForegroundWindow() or 0) == int(hwnd):
            return False

        self._user32.ShowWindow(hwnd_handle, SW_RESTORE)
        self._user32.BringWindowToTop(hwnd_handle)
        self._user32.SetForegroundWindow(hwnd_handle)
        if int(self._user32.GetForegroundWindow() or 0) == int(hwnd):
            return True

        current_thread = int(self._kernel32.GetCurrentThreadId())
        target_thread = int(self._user32.GetWindowThreadProcessId(hwnd_handle, None) or 0)
        foreground_hwnd = self._user32.GetForegroundWindow()
        foreground_thread = int(self._user32.GetWindowThreadProcessId(foreground_hwnd, None) or 0)
        attached: list[int] = []
        try:
            for thread_id in {target_thread, foreground_thread}:
                if thread_id and thread_id != current_thread:
                    if self._user32.AttachThreadInput(current_thread, thread_id, True):
                        attached.append(thread_id)
            self._user32.ShowWindow(hwnd_handle, SW_RESTORE)
            self._user32.BringWindowToTop(hwnd_handle)
            self._user32.SetForegroundWindow(hwnd_handle)
        finally:
            for thread_id in attached:
                self._user32.AttachThreadInput(current_thread, thread_id, False)
        if int(self._user32.GetForegroundWindow() or 0) == int(hwnd):
            return True

        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        self._user32.SetWindowPos(hwnd_handle, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, flags)
        self._user32.SetWindowPos(hwnd_handle, wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)
        self._user32.BringWindowToTop(hwnd_handle)
        self._user32.SetForegroundWindow(hwnd_handle)
        return True

    def _point_targets_window(self, hwnd: int, screen_x: int, screen_y: int) -> bool:
        hit = int(self._user32.WindowFromPoint(POINT(int(screen_x), int(screen_y))) or 0)
        if hit == 0:
            return False
        root = int(self._user32.GetAncestor(wintypes.HWND(hit), 2) or 0)
        return hit == int(hwnd) or root == int(hwnd)

    def _client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        point = POINT(int(x), int(y))
        if not self._user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(point)):
            raise PicoTouchError(f"ClientToScreen failed for hwnd=0x{int(hwnd):X}")
        return int(point.x), int(point.y)

    def _map_client_point(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        screen_x, screen_y = self._client_to_screen(hwnd, x, y)
        return self.config.touch_surface.map_screen_point(screen_x, screen_y)


class PicoTouchPlayer:
    def __init__(
        self,
        *,
        scheduler: PicoTouchScheduler,
        slot: int,
        hwnd: int,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.slot = int(slot)
        self.hwnd = int(hwnd)
        self.stop_requested = stop_requested
        self._last_client_pos = (0, 0)
        self._timeline_delay_sec = 0.0

    def prepare(self) -> None:
        self.scheduler.ensure_ready()

    def move(self, x: int, y: int) -> None:
        self._last_client_pos = (int(x), int(y))
        self.scheduler.move(x, y)

    def click(self, x: int, y: int, duration_sec: float = 0.0) -> None:
        self._last_client_pos = (int(x), int(y))
        self._timeline_delay_sec += float(self.scheduler.click(
            self.slot,
            self.hwnd,
            x,
            y,
            duration_sec,
            stop_requested=self.stop_requested,
        ) or 0.0)

    def left_down(self, x: int | None = None, y: int | None = None) -> None:
        x, y = self._point_or_last(x, y)
        self._last_client_pos = (x, y)
        self._timeline_delay_sec += float(self.scheduler.begin(
            self.slot,
            self.hwnd,
            x,
            y,
            stop_requested=self.stop_requested,
        ) or 0.0)

    def left_up(self, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self._last_client_pos = (int(x), int(y))
        self.scheduler.end(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float) -> None:
        self.left_down(x1, y1)
        try:
            steps = max(2, min(48, int(max(0.0, duration_sec) * 60)))
            sleep_sec = max(0.0, duration_sec) / steps
            for step in range(1, steps):
                ratio = step / steps
                self.move(round(x1 + (x2 - x1) * ratio), round(y1 + (y2 - y1) * ratio))
                if sleep_sec and not _sleep_interruptibly(sleep_sec, self.stop_requested):
                    raise PicoTouchCancelled(f"Pico playback cancelled during drag for slot {self.slot}")
        finally:
            self.left_up(x2, y2)

    def cancel(self) -> None:
        self.scheduler.cancel(expected_slot=self.slot)

    def consume_timeline_delay(self) -> float:
        delay = self._timeline_delay_sec
        self._timeline_delay_sec = 0.0
        return delay

    def _point_or_last(self, x: int | None, y: int | None) -> tuple[int, int]:
        if x is None or y is None:
            return self._last_client_pos
        return int(x), int(y)


_SCHEDULERS: dict[Path, PicoTouchScheduler] = {}
_SCHEDULERS_LOCK = threading.Lock()


def _call_stop_requested(stop_requested: Callable[[], bool] | None) -> bool:
    return bool(stop_requested and stop_requested())


def _sleep_interruptibly(seconds: float, stop_requested: Callable[[], bool] | None) -> bool:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if _call_stop_requested(stop_requested):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.02))


def _acquire_interruptibly(
    lock: threading.Lock,
    stop_requested: Callable[[], bool] | None,
) -> bool:
    if stop_requested is None:
        lock.acquire()
        return True
    while not _call_stop_requested(stop_requested):
        if lock.acquire(timeout=0.02):
            return True
    return False


def _scheduler_for_config(config_path: str | Path) -> PicoTouchScheduler:
    key = Path(config_path).resolve()
    with _SCHEDULERS_LOCK:
        scheduler = _SCHEDULERS.get(key)
        if scheduler is None:
            scheduler = PicoTouchScheduler(key)
            _SCHEDULERS[key] = scheduler
    return scheduler


def pico_touch_health_check(config_path: str | Path) -> dict[str, Any]:
    """Return Pico protocol status without targeting a game window."""
    scheduler = _scheduler_for_config(config_path)
    return {
        "port": scheduler.config.port,
        "report_mode": scheduler.config.report_mode,
        "status": scheduler.health_check(),
        "min_slot_interval_ms": scheduler.config.min_slot_interval_ms,
        "global_gesture_interval_ms": scheduler.config.global_gesture_interval_ms,
        "global_click_interval_ms": scheduler.config.global_click_interval_ms,
        "cross_slot_same_coordinate_min_ms": scheduler.config.cross_slot_same_coordinate_min_ms,
        "cross_slot_same_coordinate_max_ms": scheduler.config.cross_slot_same_coordinate_max_ms,
        "drag_release_guard_ms": scheduler.config.drag_release_guard_ms,
        "script_post_hid_guard_ms": scheduler.config.script_post_hid_guard_ms,
        "hid_drain_timeout_ms": scheduler.config.hid_drain_timeout_ms,
        "coordinate_policy": scheduler.config.coordinate_policy,
        "touch_surface": {
            "left": scheduler.config.touch_surface.left,
            "top": scheduler.config.touch_surface.top,
            "width": scheduler.config.touch_surface.width,
            "height": scheduler.config.touch_surface.height,
        },
        "layout_config": str(scheduler.layout_config_path),
    }


def pico_touch_player(
    *,
    config_path: str | Path,
    slot: int | None,
    hwnd: int,
    stop_requested: Callable[[], bool] | None = None,
) -> PicoTouchPlayer:
    if slot is None:
        raise PicoTouchError("Pico HID touch playback requires an explicit slot number")
    scheduler = _scheduler_for_config(config_path)
    return PicoTouchPlayer(
        scheduler=scheduler,
        slot=slot,
        hwnd=hwnd,
        stop_requested=stop_requested,
    )


def _foreground_api() -> Any:
    if ctypes.sizeof(ctypes.c_void_p) == 0 or not hasattr(ctypes, "WinDLL"):
        raise PicoTouchError("Pico HID touch playback requires Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    return user32


def _kernel32_api() -> Any:
    if ctypes.sizeof(ctypes.c_void_p) == 0 or not hasattr(ctypes, "WinDLL"):
        raise PicoTouchError("Pico HID touch playback requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return kernel32
