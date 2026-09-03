from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import math
import os
from pathlib import Path
import queue
import re
import signal
import threading
import time
from typing import Any, Callable, Sequence

from .json_io import read_json_file, write_json_file
from .slot_limits import MAX_SLOT, MIN_SLOT
from .windows_device import client_size, enable_physical_dpi_coordinates, resolve_window, screen_to_client


WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
PM_REMOVE = 0x0001

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
VK_F8 = 0x77

LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_INJECTED = 0x00000010

SCRIPT_FORMAT = "gui_test_pc_script_v1"
LEGACY_SCRIPT_FORMATS = {"gui_test_pc_script"}
SCRIPT_VERSION = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_RESTORE = 9

PLAYBACK_BACKEND_WINDOW_MESSAGE = "window_message"
PLAYBACK_BACKEND_FOREGROUND_MOUSE = "foreground_mouse"
PLAYBACK_BACKEND_PICO_HID_TOUCH = "pico_hid_touch"

FORCED_DRAG_INITIAL_MOVE_MS = 30
FORCED_DRAG_MIN_INITIAL_MOVE_PX = 24

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t


def assign_scripts_round_robin(
    scripts: Sequence[Path],
    slots: Sequence[int],
) -> dict[int, Path]:
    """Assign exactly one script to each slot, cycling in slot order."""
    if not scripts:
        return {}
    return {
        int(slot): scripts[index % len(scripts)]
        for index, slot in enumerate(slots)
    }


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def default_script_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("scripts_pc") / f"recording_{stamp}.pcscript.json"


def record_pc_script(
    *,
    output: str | Path | None = None,
    seconds: float = 30.0,
    hwnd: str | int | None = None,
    title_contains: str | None = None,
    process_name: str | None = None,
    process_path: str | Path | None = None,
    process_path_prefix: str | Path | None = None,
    match_index: int | None = None,
    min_drag_px: int | None = None,
    click_max_duration_ms: int = 180,
    click_max_move_px: int = 3,
    target_metadata: dict[str, Any] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    _require_windows()
    enable_physical_dpi_coordinates()
    info = resolve_window(
        hwnd=hwnd,
        title_contains=title_contains,
        process_name=process_name,
        process_path=process_path,
        process_path_prefix=process_path_prefix,
        match_index=match_index,
    )
    _activate_foreground_window(info.hwnd)
    width, height = client_size(info.hwnd)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"target window has empty client area: hwnd=0x{info.hwnd:X}")

    if min_drag_px is not None:
        click_max_move_px = int(min_drag_px)
    recorder = _MouseScriptRecorder(
        hwnd=info.hwnd,
        width=width,
        height=height,
        click_max_duration_ms=max(1, int(click_max_duration_ms)),
        click_max_move_px=max(0, int(click_max_move_px)),
        status_callback=status_callback,
    )
    recording = recorder.record(seconds=max(0.0, float(seconds)), stop_requested=stop_requested)
    events = recording["events"]
    target = _script_target(info.to_dict(), target_metadata)
    script = {
        "format": SCRIPT_FORMAT,
        "version": SCRIPT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": target,
        "client_size": {"w": width, "h": height},
        "duration_ms": recording["duration_ms"],
        "recording_policy": recording["recording_policy"],
        "events": events,
    }
    target_path = Path(output) if output else default_script_path()
    write_json_file(target_path, script)
    return {
        "path": str(target_path),
        "event_count": len(events),
        "client_size": script["client_size"],
        "duration_ms": script["duration_ms"],
        "recording_policy": script["recording_policy"],
        "target": target,
        "events": events,
    }


def play_pc_script(
    *,
    script_path: str | Path,
    hwnd: str | int | None = None,
    title_contains: str | None = None,
    process_name: str | None = None,
    process_path: str | Path | None = None,
    process_path_prefix: str | Path | None = None,
    match_index: int | None = None,
    speed: float = 1.0,
    allow_size_mismatch: bool = False,
    dry_run: bool = False,
    expected_slot: int | None = None,
    backend: str = PLAYBACK_BACKEND_WINDOW_MESSAGE,
    pico_config_path: str | Path | None = None,
    stop_requested: Callable[[], bool] | None = None,
    playback_coordinator: Any | None = None,
    playback_handle: int | None = None,
    timeline_started_at: float | None = None,
) -> dict[str, Any]:
    script = _load_script(script_path)
    events = list(script.get("events", []))
    duration_ms = _script_duration_ms(script, events)
    speed_factor = float(speed)
    if speed_factor <= 0:
        raise RuntimeError("speed must be greater than 0")
    playback_backend = _normalize_playback_backend(backend)
    pico_drag_timing: dict[str, Any] | None = None
    if playback_backend == PLAYBACK_BACKEND_PICO_HID_TOUCH:
        events, duration_ms, pico_drag_timing = _retime_pico_drag_events(
            events,
            duration_ms=duration_ms,
            pico_config_path=pico_config_path,
        )

    if dry_run:
        result = {
            "script": str(script_path),
            "dry_run": True,
            "backend": playback_backend,
            "event_count": len(events),
            "duration_ms": duration_ms,
            "total_play_ms": round(duration_ms / speed_factor),
            "actions": [_event_action_preview(event, speed_factor) for event in events],
        }
        if pico_drag_timing is not None:
            result["pico_drag_timing"] = pico_drag_timing
        return result

    _require_windows()
    if playback_backend == PLAYBACK_BACKEND_PICO_HID_TOUCH:
        enable_physical_dpi_coordinates()
    info = resolve_window(
        hwnd=hwnd,
        title_contains=title_contains,
        process_name=process_name,
        process_path=process_path,
        process_path_prefix=process_path_prefix,
        match_index=match_index,
    )
    _validate_expected_slot(info.to_dict(), expected_slot)
    current_w, current_h = client_size(info.hwnd)
    recorded_size = script.get("client_size", {})
    recorded_w = int(recorded_size.get("w", 0))
    recorded_h = int(recorded_size.get("h", 0))
    size_mismatch = current_w != recorded_w or current_h != recorded_h
    same_aspect_resize = (
        min(recorded_w, recorded_h, current_w, current_h) >= 2
        and recorded_w * current_h == current_w * recorded_h
    )
    if not allow_size_mismatch and size_mismatch and not same_aspect_resize:
        raise RuntimeError(
            "client size mismatch: "
            f"recorded={recorded_w}x{recorded_h}, current={current_w}x{current_h}. "
            "Only same-aspect production scaling is automatic; otherwise pass "
            "--allow-size-mismatch for local QA."
        )
    if size_mismatch and (recorded_w < 2 or recorded_h < 2 or current_w < 2 or current_h < 2):
        raise RuntimeError(
            "cannot scale client coordinates because recorded/current client dimensions must be at least 2"
        )
    coordinate_scale = {
        "applied": size_mismatch,
        "recorded": {"w": recorded_w, "h": recorded_h},
        "current": {"w": current_w, "h": current_h},
    }

    actions: list[dict[str, Any]] = []
    if (playback_coordinator is None) != (playback_handle is None):
        raise RuntimeError(
            "playback_coordinator and playback_handle must be provided together"
        )
    player = _make_player(
        playback_backend,
        info.hwnd,
        expected_slot=expected_slot,
        pico_config_path=pico_config_path,
        stop_requested=stop_requested,
    )
    player.prepare()
    started = (
        float(timeline_started_at)
        if timeline_started_at is not None
        else time.monotonic()
    )
    timeline_delay_sec = 0.0
    scheduler_delay_sec = 0.0
    drag_active = False
    coordinator_turn_held = False
    cancelled = _call_stop_requested(stop_requested)
    try:
        for index, event in enumerate(events):
            event_play_time = _event_play_time_sec(event, speed_factor) + timeline_delay_sec
            if playback_coordinator is not None and not drag_active:
                coordinator_delay = playback_coordinator.wait_for_turn(
                    int(playback_handle),
                    started + event_play_time,
                    stop_requested=stop_requested,
                )
                if coordinator_delay is None:
                    cancelled = True
                    break
                coordinator_turn_held = True
                scheduler_delay_sec += max(0.0, float(coordinator_delay))
                timeline_delay_sec += max(0.0, float(coordinator_delay))
            elif cancelled or not _wait_until(
                started,
                event_play_time,
                stop_requested=stop_requested,
            ):
                cancelled = True
                break
            playback_event = _scale_event_for_client_size(
                event,
                recorded_w=recorded_w,
                recorded_h=recorded_h,
                current_w=current_w,
                current_h=current_h,
            ) if size_mismatch else event
            try:
                action, drag_active = _play_event(player, playback_event, speed_factor, drag_active)
            except Exception:
                if _call_stop_requested(stop_requested):
                    cancelled = True
                    break
                raise
            action["index"] = index
            actions.append(action)
            if hasattr(player, "consume_timeline_delay"):
                timeline_delay_sec += max(0.0, float(player.consume_timeline_delay()))
            if (
                playback_coordinator is not None
                and coordinator_turn_held
                and not drag_active
            ):
                if index + 1 < len(events):
                    next_play_time = (
                        _event_play_time_sec(events[index + 1], speed_factor)
                        + timeline_delay_sec
                    )
                else:
                    next_play_time = (
                        duration_ms / 1000.0 / speed_factor
                        + timeline_delay_sec
                    )
                playback_coordinator.release_turn(
                    int(playback_handle),
                    next_deadline=started + next_play_time,
                )
                coordinator_turn_held = False
        if not cancelled:
            end_play_time = duration_ms / 1000.0 / speed_factor + timeline_delay_sec
            if playback_coordinator is not None:
                coordinator_delay = playback_coordinator.wait_for_turn(
                    int(playback_handle),
                    started + end_play_time,
                    stop_requested=stop_requested,
                )
                if coordinator_delay is None:
                    cancelled = True
                else:
                    coordinator_turn_held = True
                    scheduler_delay_sec += max(0.0, float(coordinator_delay))
                    timeline_delay_sec += max(0.0, float(coordinator_delay))
                    playback_coordinator.release_turn(int(playback_handle))
                    coordinator_turn_held = False
            elif not _wait_until(
                started,
                end_play_time,
                stop_requested=stop_requested,
            ):
                cancelled = True
    finally:
        if cancelled and hasattr(player, "cancel"):
            player.cancel()
            drag_active = False
        elif drag_active:
            try:
                player.left_up()
            except Exception:
                if hasattr(player, "cancel"):
                    player.cancel()
                raise
        if playback_coordinator is not None and coordinator_turn_held:
            playback_coordinator.release_turn(int(playback_handle))
            coordinator_turn_held = False
    result = {
        "script": str(script_path),
        "dry_run": False,
        "backend": playback_backend,
        "event_count": len(events),
        "duration_ms": duration_ms,
        "timeline_delay_ms": round(timeline_delay_sec * 1000),
        "scheduler_delay_ms": round(scheduler_delay_sec * 1000),
        "cancelled": cancelled,
        "target": {"hwnd": info.hwnd, "hwnd_hex": f"0x{info.hwnd:X}", "title": info.title, "pid": info.pid},
        "client_size": {"w": current_w, "h": current_h},
        "coordinate_scale": coordinate_scale,
        "actions": actions,
    }
    if pico_drag_timing is not None:
        result["pico_drag_timing"] = pico_drag_timing
    return result


class _MouseScriptRecorder:
    def __init__(
        self,
        *,
        hwnd: int,
        width: int,
        height: int,
        click_max_duration_ms: int,
        click_max_move_px: int,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.hwnd = hwnd
        self.width = width
        self.height = height
        self.click_max_duration_ms = click_max_duration_ms
        self.click_max_move_px = click_max_move_px
        self._status_callback = status_callback
        self._raw_events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._script_events: list[dict[str, Any]] = []
        self._active: dict[str, Any] | None = None
        self._force_drag_armed = False
        self._force_drag_key_down = False
        self._started = 0.0
        self._stop = False

    def record(
        self,
        *,
        seconds: float,
        stop_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        user32, kernel32 = _input_api()

        def mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == HC_ACTION:
                message = int(w_param)
                if message in (WM_LBUTTONDOWN, WM_MOUSEMOVE, WM_LBUTTONUP):
                    raw = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self._raw_events.put(
                        {
                            "kind": "mouse",
                            "message": message,
                            "x": int(raw.pt.x),
                            "y": int(raw.pt.y),
                            "flags": int(raw.flags),
                        }
                    )
            return user32.CallNextHookEx(0, n_code, w_param, l_param)

        def keyboard_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == HC_ACTION:
                message = int(w_param)
                raw = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                flags = int(raw.flags)
                if int(raw.vkCode) == VK_F8 and not flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if not self._force_drag_key_down:
                            self._force_drag_key_down = True
                            self._raw_events.put({"kind": "force_drag"})
                    elif message in (WM_KEYUP, WM_SYSKEYUP):
                        self._force_drag_key_down = False
                    return 1
            return user32.CallNextHookEx(0, n_code, w_param, l_param)

        mouse_callback = HOOKPROC(mouse_proc)
        keyboard_callback = HOOKPROC(keyboard_proc)
        module = kernel32.GetModuleHandleW(None)
        mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, module, 0)
        if not mouse_hook:
            raise RuntimeError(f"SetWindowsHookExW(WH_MOUSE_LL) failed: WinError {ctypes.get_last_error()}")
        keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_callback, module, 0)
        if not keyboard_hook:
            error = ctypes.get_last_error()
            user32.UnhookWindowsHookEx(mouse_hook)
            raise RuntimeError(f"SetWindowsHookExW(WH_KEYBOARD_LL) failed: WinError {error}")

        previous_sigint: Any = None
        restore_sigint = threading.current_thread() is threading.main_thread()

        def handle_sigint(_signum, _frame) -> None:
            self._stop = True

        if restore_sigint:
            previous_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, handle_sigint)
        self._started = time.monotonic()
        self._emit_status("錄製中：一般判定；F8 未啟用")
        deadline = self._started + seconds if seconds > 0 else float("inf")
        duration_ms = 0
        try:
            while not self._stop and not _call_stop_requested(stop_requested) and time.monotonic() < deadline:
                _pump_messages(user32)
                self._drain_raw_events()
                time.sleep(0.01)
            _pump_messages(user32)
            self._drain_raw_events()
        finally:
            duration_ms = round((time.monotonic() - self._started) * 1000)
            if restore_sigint:
                signal.signal(signal.SIGINT, previous_sigint)
            unhook_errors: list[str] = []
            for hook_name, hook in (("keyboard", keyboard_hook), ("mouse", mouse_hook)):
                if hook and not user32.UnhookWindowsHookEx(hook):
                    unhook_errors.append(f"{hook_name}=WinError {ctypes.get_last_error()}")
            if unhook_errors:
                raise RuntimeError(f"UnhookWindowsHookEx failed: {', '.join(unhook_errors)}")
        return {
            "events": self._script_events,
            "duration_ms": duration_ms,
            "recording_policy": {
                "classification": "strict_click_else_drag",
                "click_max_duration_ms": self.click_max_duration_ms,
                "click_max_move_px": self.click_max_move_px,
                "force_drag_hotkey": "F8",
            },
        }

    def _drain_raw_events(self) -> None:
        while True:
            try:
                event = self._raw_events.get_nowait()
            except queue.Empty:
                return
            self._handle_raw_event(event)

    def _handle_raw_event(self, event: dict[str, Any]) -> None:
        if event.get("kind") == "force_drag":
            if self._active is not None:
                self._active["force_drag"] = True
                self._emit_status("目前手勢：強制拖曳")
            else:
                self._force_drag_armed = True
                self._emit_status("下一個手勢：強制拖曳（一次性）")
            return
        if event["flags"] & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED):
            return
        client_x, client_y = screen_to_client(self.hwnd, event["x"], event["y"])
        inside = self._inside(client_x, client_y)
        event_ms = round((time.monotonic() - self._started) * 1000)
        message = event["message"]

        if message == WM_LBUTTONDOWN:
            if not inside:
                self._active = None
                return
            self._active = {
                "t_ms": event_ms,
                "x": client_x,
                "y": client_y,
                "moves": [],
                "force_drag": self._force_drag_armed,
            }
            self._force_drag_armed = False
            if self._active["force_drag"]:
                self._emit_status("目前手勢：強制拖曳")
            else:
                self._emit_status("目前手勢：等待鬆手判定")
            return

        if self._active is None:
            return

        if message == WM_MOUSEMOVE:
            move_x, move_y = self._clamp_point(client_x, client_y)
            moves = self._active["moves"]
            if not moves or moves[-1]["x"] != move_x or moves[-1]["y"] != move_y:
                moves.append({"type": "drag_move", "t_ms": event_ms, "x": move_x, "y": move_y})
            return

        if message == WM_LBUTTONUP:
            end_x, end_y = self._clamp_point(client_x, client_y)
            start_x = int(self._active["x"])
            start_y = int(self._active["y"])
            duration_ms = max(0, event_ms - int(self._active["t_ms"]))
            gesture_type = _classify_recorded_gesture(
                duration_ms=duration_ms,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                moves=self._active["moves"],
                force_drag=bool(self._active["force_drag"]),
                click_max_duration_ms=self.click_max_duration_ms,
                click_max_move_px=self.click_max_move_px,
            )
            if gesture_type == "click":
                self._script_events.append(
                    {
                        "type": "click",
                        "t_ms": int(self._active["t_ms"]),
                        "x": start_x,
                        "y": start_y,
                        "button": "left",
                        "duration_ms": duration_ms,
                    }
                )
            else:
                drag_id = f"drag_{len(self._script_events) + 1:04d}"
                self._script_events.append(
                    {
                        "type": "drag_start",
                        "t_ms": int(self._active["t_ms"]),
                        "x": start_x,
                        "y": start_y,
                        "button": "left",
                        "drag_id": drag_id,
                        "forced_drag": bool(self._active["force_drag"]),
                    }
                )
                for move in self._active["moves"]:
                    move_t = int(move["t_ms"])
                    if move_t <= int(self._active["t_ms"]) or move_t >= event_ms:
                        continue
                    self._script_events.append(
                        {
                            "type": "drag_move",
                            "t_ms": move_t,
                            "x": int(move["x"]),
                            "y": int(move["y"]),
                            "drag_id": drag_id,
                        }
                    )
                self._script_events.append(
                    {
                        "type": "drag_end",
                        "t_ms": event_ms,
                        "x": end_x,
                        "y": end_y,
                        "button": "left",
                        "drag_id": drag_id,
                        "duration_ms": duration_ms,
                    }
                )
            self._active = None
            result_label = "點擊" if gesture_type == "click" else "拖曳"
            self._emit_status(f"上一個手勢：{result_label}；F8 未啟用")

    def _emit_status(self, message: str) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(message)
        except Exception:
            pass

    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def _clamp_point(self, x: int, y: int) -> tuple[int, int]:
        return _clamp(x, 0, self.width - 1), _clamp(y, 0, self.height - 1)


class _ForegroundMousePlayer:
    def __init__(self, hwnd: int) -> None:
        self.hwnd = int(hwnd)
        self.user32 = _foreground_input_api()
        self._last_client_pos = (0, 0)

    def prepare(self) -> None:
        _activate_foreground_window(self.hwnd, user32=self.user32)

    def move(self, x: int, y: int) -> None:
        screen_x, screen_y = self._client_to_screen(x, y)
        if not self.user32.SetCursorPos(screen_x, screen_y):
            raise RuntimeError(f"SetCursorPos failed: WinError {ctypes.get_last_error()}")
        self._last_client_pos = (int(x), int(y))

    def click(self, x: int, y: int, duration_sec: float = 0.0) -> None:
        self.move(x, y)
        self.left_down(x, y)
        if duration_sec > 0:
            time.sleep(duration_sec)
        self.left_up(x, y)

    def left_down(self, x: int, y: int) -> None:
        self.move(x, y)
        self.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

    def left_up(self, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        self.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float) -> None:
        self.left_down(x1, y1)
        steps = max(2, min(48, int(max(0.0, duration_sec) * 60)))
        sleep_sec = max(0.0, duration_sec) / steps
        for step in range(1, steps):
            ratio = step / steps
            self.move(round(x1 + (x2 - x1) * ratio), round(y1 + (y2 - y1) * ratio))
            if sleep_sec:
                time.sleep(sleep_sec)
        self.left_up(x2, y2)

    def _client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        point = POINT(int(x), int(y))
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(point)):
            raise RuntimeError(f"ClientToScreen failed for hwnd=0x{self.hwnd:X}")
        return int(point.x), int(point.y)


class _WindowMessagePlayer:
    def __init__(self, hwnd: int) -> None:
        self.hwnd = int(hwnd)
        self.user32 = _window_message_api()
        self._last_client_pos = (0, 0)
        self._button_down = False

    def prepare(self) -> None:
        return

    def move(self, x: int, y: int) -> None:
        self._post(WM_MOUSEMOVE, x, y, MK_LBUTTON if self._button_down else 0)

    def click(self, x: int, y: int, duration_sec: float = 0.0) -> None:
        self.move(x, y)
        self.left_down(x, y)
        if duration_sec > 0:
            time.sleep(duration_sec)
        self.left_up(x, y)

    def left_down(self, x: int | None = None, y: int | None = None) -> None:
        x, y = self._point_or_last(x, y)
        self.move(x, y)
        self._button_down = True
        self._post(WM_LBUTTONDOWN, x, y, MK_LBUTTON)

    def left_up(self, x: int | None = None, y: int | None = None) -> None:
        x, y = self._point_or_last(x, y)
        if self._button_down:
            self.move(x, y)
        self._post(WM_LBUTTONUP, x, y, 0)
        self._button_down = False

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float) -> None:
        self.left_down(x1, y1)
        steps = max(2, min(48, int(max(0.0, duration_sec) * 60)))
        sleep_sec = max(0.0, duration_sec) / steps
        for step in range(1, steps):
            ratio = step / steps
            self.move(round(x1 + (x2 - x1) * ratio), round(y1 + (y2 - y1) * ratio))
            if sleep_sec:
                time.sleep(sleep_sec)
        self.left_up(x2, y2)

    def _point_or_last(self, x: int | None, y: int | None) -> tuple[int, int]:
        if x is None or y is None:
            return self._last_client_pos
        return int(x), int(y)

    def _post(self, message: int, x: int, y: int, w_param: int) -> None:
        x = int(x)
        y = int(y)
        self._last_client_pos = (x, y)
        if not self.user32.PostMessageW(self.hwnd, message, int(w_param), _make_mouse_lparam(x, y)):
            raise RuntimeError(
                f"PostMessageW failed: hwnd=0x{self.hwnd:X} message=0x{message:X} WinError {ctypes.get_last_error()}"
            )


def _make_player(
    backend: str,
    hwnd: int,
    *,
    expected_slot: int | None = None,
    pico_config_path: str | Path | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> Any:
    if backend == PLAYBACK_BACKEND_WINDOW_MESSAGE:
        return _WindowMessagePlayer(hwnd)
    if backend == PLAYBACK_BACKEND_FOREGROUND_MOUSE:
        return _ForegroundMousePlayer(hwnd)
    if backend == PLAYBACK_BACKEND_PICO_HID_TOUCH:
        if pico_config_path is None:
            raise RuntimeError("Pico HID touch playback requires pico_config_path")
        from .pico_touch import pico_touch_player

        return pico_touch_player(
            config_path=pico_config_path,
            slot=expected_slot,
            hwnd=hwnd,
            stop_requested=stop_requested,
        )
    raise RuntimeError(f"unsupported playback backend: {backend!r}")


def _play_event(
    player: Any,
    event: dict[str, Any],
    speed: float,
    drag_active: bool,
) -> tuple[dict[str, Any], bool]:
    event_type = str(event.get("type", ""))
    if event_type in {"tap", "click"}:
        x = int(event["x"])
        y = int(event["y"])
        if drag_active:
            player.left_up()
            drag_active = False
        duration_sec = max(0.0, float(event.get("duration_ms", 0)) / 1000.0 / speed)
        player.click(x, y, duration_sec)
        return {"type": "click", "x": x, "y": y, "duration_ms": round(duration_sec * 1000)}, drag_active
    if event_type == "drag":
        x1 = int(event["x1"])
        y1 = int(event["y1"])
        x2 = int(event["x2"])
        y2 = int(event["y2"])
        duration_sec = max(0.0, float(event.get("duration_ms", 0)) / 1000.0 / speed)
        player.drag(x1, y1, x2, y2, duration_sec)
        return {
            "type": "drag",
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "duration_ms": round(duration_sec * 1000),
        }, drag_active
    if event_type == "drag_start":
        if drag_active:
            player.left_up()
        x = int(event["x"])
        y = int(event["y"])
        player.left_down(x, y)
        return {"type": "drag_start", "x": x, "y": y}, True
    if event_type == "drag_move":
        x = int(event["x"])
        y = int(event["y"])
        player.move(x, y)
        return {"type": "drag_move", "x": x, "y": y}, drag_active
    if event_type == "drag_end":
        x = int(event["x"])
        y = int(event["y"])
        player.left_up(x, y)
        return {"type": "drag_end", "x": x, "y": y}, False
    raise RuntimeError(f"unsupported PC script event type: {event_type!r}")


def _event_action_preview(event: dict[str, Any], speed: float) -> dict[str, Any]:
    preview = dict(event)
    preview["play_at_ms"] = round(_event_play_time_sec(event, speed) * 1000)
    if preview.get("type") in {"click", "tap", "drag"}:
        preview["play_duration_ms"] = round(max(0.0, float(event.get("duration_ms", 0)) / speed))
    return preview


def _scale_event_for_client_size(
    event: dict[str, Any],
    *,
    recorded_w: int,
    recorded_h: int,
    current_w: int,
    current_h: int,
) -> dict[str, Any]:
    scaled = dict(event)
    for x_name, y_name in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
        if x_name in scaled or y_name in scaled:
            if x_name not in scaled or y_name not in scaled:
                raise RuntimeError(f"script event has incomplete coordinate pair: {x_name}/{y_name}")
            scaled[x_name] = _scale_coordinate(int(scaled[x_name]), recorded_w, current_w)
            scaled[y_name] = _scale_coordinate(int(scaled[y_name]), recorded_h, current_h)
    return scaled


def _scale_coordinate(value: int, recorded_size: int, current_size: int) -> int:
    return _clamp(round(value * (current_size - 1) / (recorded_size - 1)), 0, current_size - 1)


def _retime_pico_drag_events(
    events: list[dict[str, Any]],
    *,
    duration_ms: int,
    pico_config_path: str | Path | None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    if pico_config_path is None:
        return events, duration_ms, None
    config = read_json_file(Path(pico_config_path))
    if not isinstance(config, dict):
        return events, duration_ms, None
    hold_before_ms = max(0, int(config.get("drag_hold_before_ms", 0)))
    min_duration_ms = max(0, int(config.get("drag_min_duration_ms", 0)))
    hold_after_ms = max(0, int(config.get("drag_hold_after_ms", 0)))
    resample_steps = max(0, int(config.get("drag_resample_steps", 0)))
    if hold_before_ms == 0 and min_duration_ms == 0 and hold_after_ms == 0 and resample_steps == 0:
        return events, duration_ms, None

    retimed: list[dict[str, Any]] = []
    offset_ms = 0
    drag_groups = 0
    forced_drag_groups = 0
    inserted_moves = 0
    i = 0
    while i < len(events):
        event = events[i]
        if str(event.get("type")) != "drag_start":
            copied = dict(event)
            copied["t_ms"] = int(round(float(copied.get("t_ms", 0)))) + offset_ms
            retimed.append(copied)
            i += 1
            continue

        group: list[dict[str, Any]] = [event]
        drag_id = event.get("drag_id")
        end_index: int | None = None
        j = i + 1
        while j < len(events):
            group.append(events[j])
            if str(events[j].get("type")) == "drag_end" and (
                drag_id is None or events[j].get("drag_id") == drag_id
            ):
                end_index = j
                break
            j += 1
        if end_index is None:
            copied = dict(event)
            copied["t_ms"] = int(round(float(copied.get("t_ms", 0)))) + offset_ms
            retimed.append(copied)
            i += 1
            continue

        new_group, new_end_t = _retime_one_pico_drag_group(
            group,
            offset_ms=offset_ms,
            hold_before_ms=hold_before_ms,
            min_duration_ms=min_duration_ms,
            hold_after_ms=hold_after_ms,
            resample_steps=resample_steps,
        )
        original_end_t = int(round(float(group[-1].get("t_ms", 0))))
        offset_ms += new_end_t - (original_end_t + offset_ms)
        drag_groups += 1
        if bool(event.get("forced_drag")):
            forced_drag_groups += 1
        inserted_moves += max(0, sum(1 for item in new_group if item.get("type") == "drag_move") - sum(1 for item in group if item.get("type") == "drag_move"))
        retimed.extend(new_group)
        i = end_index + 1

    profile = {
        "applied": True,
        "drag_groups": drag_groups,
        "forced_drag_groups": forced_drag_groups,
        "added_duration_ms": offset_ms,
        "inserted_moves": inserted_moves,
        "hold_before_ms": hold_before_ms,
        "min_duration_ms": min_duration_ms,
        "hold_after_ms": hold_after_ms,
        "resample_steps": resample_steps,
        "forced_drag_initial_move_ms": FORCED_DRAG_INITIAL_MOVE_MS,
        "forced_drag_min_initial_move_px": FORCED_DRAG_MIN_INITIAL_MOVE_PX,
    }
    return retimed, duration_ms + offset_ms, profile


def _retime_one_pico_drag_group(
    group: list[dict[str, Any]],
    *,
    offset_ms: int,
    hold_before_ms: int,
    min_duration_ms: int,
    hold_after_ms: int,
    resample_steps: int,
) -> tuple[list[dict[str, Any]], int]:
    start = group[0]
    end = group[-1]
    original_start_t = int(round(float(start.get("t_ms", 0))))
    original_end_t = int(round(float(end.get("t_ms", original_start_t))))
    original_duration_ms = max(1, original_end_t - original_start_t)
    body_duration_ms = max(original_duration_ms, min_duration_ms)
    new_start_t = original_start_t + offset_ms

    points = [
        (
            0.0,
            int(start["x"]),
            int(start["y"]),
        )
    ]
    for event in group[1:-1]:
        if str(event.get("type")) != "drag_move":
            continue
        event_t = int(round(float(event.get("t_ms", original_start_t))))
        ratio = _clamp_float((event_t - original_start_t) / original_duration_ms, 0.0, 1.0)
        points.append((ratio, int(event["x"]), int(event["y"])))
    points.append((1.0, int(end["x"]), int(end["y"])))
    points.sort(key=lambda item: item[0])
    forced_drag = bool(start.get("forced_drag"))
    forced_arm_ratio = 0.0
    forced_arm_ms = 0
    forced_arm_point: tuple[int, int] | None = None
    if forced_drag:
        path_length = _drag_path_length(points)
        if path_length < FORCED_DRAG_MIN_INITIAL_MOVE_PX:
            raise RuntimeError(
                "forced F8 drag path is too short to guarantee non-click playback: "
                f"drag_id={start.get('drag_id')} path={path_length:.1f}px "
                f"required={FORCED_DRAG_MIN_INITIAL_MOVE_PX}px"
            )
        forced_arm_ratio, arm_x, arm_y = _point_at_drag_path_distance(
            points,
            FORCED_DRAG_MIN_INITIAL_MOVE_PX,
        )
        forced_arm_point = (arm_x, arm_y)
        forced_arm_ms = min(FORCED_DRAG_INITIAL_MOVE_MS, max(1, body_duration_ms))

    original_move_count = sum(1 for item in group if str(item.get("type")) == "drag_move")
    steps = max(1, original_move_count, resample_steps)
    drag_id = start.get("drag_id")

    retimed: list[dict[str, Any]] = []
    start_event = dict(start)
    start_event["t_ms"] = new_start_t
    retimed.append(start_event)
    previous_point: tuple[int, int] | None = None
    if forced_arm_point is not None:
        arm_event = {
            "type": "drag_move",
            "t_ms": int(new_start_t + forced_arm_ms),
            "x": forced_arm_point[0],
            "y": forced_arm_point[1],
            "forced_drag_arm": True,
        }
        if drag_id is not None:
            arm_event["drag_id"] = drag_id
        retimed.append(arm_event)
        previous_point = forced_arm_point
    for step in range(1, steps + 1):
        ratio = step / steps
        if forced_drag and ratio <= forced_arm_ratio:
            continue
        x, y = _sample_drag_path(points, ratio)
        if previous_point == (x, y) and step != steps:
            continue
        if forced_drag:
            remaining_ratio = (ratio - forced_arm_ratio) / max(1e-9, 1.0 - forced_arm_ratio)
            move_t = new_start_t + forced_arm_ms + round(
                max(0, body_duration_ms - forced_arm_ms) * remaining_ratio
            )
        else:
            move_t = new_start_t + hold_before_ms + round(body_duration_ms * ratio)
        move_event = {
            "type": "drag_move",
            "t_ms": int(move_t),
            "x": x,
            "y": y,
        }
        if drag_id is not None:
            move_event["drag_id"] = drag_id
        retimed.append(move_event)
        previous_point = (x, y)

    effective_hold_before_ms = 0 if forced_drag else hold_before_ms
    new_end_t = new_start_t + effective_hold_before_ms + body_duration_ms + hold_after_ms
    end_event = dict(end)
    end_event["t_ms"] = int(new_end_t)
    end_event["duration_ms"] = int(new_end_t - new_start_t)
    retimed.append(end_event)
    return retimed, int(new_end_t)


def _sample_drag_path(points: list[tuple[float, int, int]], ratio: float) -> tuple[int, int]:
    ratio = _clamp_float(ratio, 0.0, 1.0)
    previous = points[0]
    for current in points[1:]:
        if ratio <= current[0]:
            span = current[0] - previous[0]
            local = 0.0 if span <= 0 else (ratio - previous[0]) / span
            x = round(previous[1] + (current[1] - previous[1]) * local)
            y = round(previous[2] + (current[2] - previous[2]) * local)
            return int(x), int(y)
        previous = current
    return points[-1][1], points[-1][2]


def _drag_path_length(points: list[tuple[float, int, int]]) -> float:
    distance = 0.0
    for previous, current in zip(points, points[1:]):
        distance += math.hypot(current[1] - previous[1], current[2] - previous[2])
    return distance


def _point_at_drag_path_distance(
    points: list[tuple[float, int, int]],
    target_distance: float,
) -> tuple[float, int, int]:
    remaining = max(0.0, float(target_distance))
    for previous, current in zip(points, points[1:]):
        segment = math.hypot(current[1] - previous[1], current[2] - previous[2])
        if segment <= 0:
            continue
        if remaining <= segment:
            local = remaining / segment
            ratio = previous[0] + (current[0] - previous[0]) * local
            x = round(previous[1] + (current[1] - previous[1]) * local)
            y = round(previous[2] + (current[2] - previous[2]) * local)
            return float(ratio), int(x), int(y)
        remaining -= segment
    return points[-1]


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _normalize_playback_backend(value: str | None) -> str:
    text = str(value or PLAYBACK_BACKEND_WINDOW_MESSAGE).strip().casefold().replace("-", "_")
    if text in {"", "background", "message", "postmessage", "post_message", "window_message"}:
        return PLAYBACK_BACKEND_WINDOW_MESSAGE
    if text in {"foreground", "foreground_mouse", "mouse", "physical_mouse"}:
        return PLAYBACK_BACKEND_FOREGROUND_MOUSE
    if text in {"pico", "pico_hid", "pico_hid_touch", "hid_touch"}:
        return PLAYBACK_BACKEND_PICO_HID_TOUCH
    raise RuntimeError(f"unsupported playback backend: {value!r}")


def _make_mouse_lparam(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _load_script(path: str | Path) -> dict[str, Any]:
    script = read_json_file(Path(path))
    if not isinstance(script, dict):
        raise RuntimeError("PC script must be a JSON object")
    script_format = script.get("format")
    if script_format != SCRIPT_FORMAT and script_format not in LEGACY_SCRIPT_FORMATS:
        raise RuntimeError(f"unsupported PC script format: {script.get('format')!r}")
    if int(script.get("version", 0)) != SCRIPT_VERSION:
        raise RuntimeError(f"unsupported PC script version: {script.get('version')!r}")
    events = script.get("events")
    if not isinstance(events, list):
        raise RuntimeError("PC script events must be a list")
    return script


def _script_target(info: dict[str, Any], target_metadata: dict[str, Any] | None) -> dict[str, Any]:
    target = dict(info)
    if "pid" in target and "process_id" not in target:
        target["process_id"] = target["pid"]
    if target_metadata:
        for key, value in target_metadata.items():
            if value is not None:
                target[key] = value
    return target


def _script_duration_ms(script: dict[str, Any], events: list[dict[str, Any]]) -> int:
    value = script.get("duration_ms")
    if value is not None:
        return max(0, int(round(float(value))))
    duration = 0
    for event in events:
        event_t = int(round(float(event.get("t_ms", 0))))
        event_duration = int(round(float(event.get("duration_ms", 0))))
        duration = max(duration, event_t + event_duration)
    return duration


def _event_play_time_sec(event: dict[str, Any], speed: float) -> float:
    return max(0.0, float(event.get("t_ms", 0)) / 1000.0 / speed)


def _wait_until(
    started: float,
    play_time_sec: float,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    while True:
        if _call_stop_requested(stop_requested):
            return False
        wait_sec = play_time_sec - (time.monotonic() - started)
        if wait_sec <= 0:
            return True
        time.sleep(min(wait_sec, 0.02))


def _validate_expected_slot(target: dict[str, Any], expected_slot: int | None) -> None:
    if expected_slot is None:
        return
    title = str(target.get("title") or "")
    title_slot = _slot_from_title(title)
    if title_slot is not None and title_slot != int(expected_slot):
        raise RuntimeError(f"target slot mismatch: expected slot {expected_slot}, title={title!r}")


def _slot_from_title(title: str) -> int | None:
    match = None
    for pattern in (r"\[(\d{1,2})\]", r"\bSCG(\d{1,3})\b"):
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            break
    if not match:
        return None
    slot = int(match.group(1))
    return slot if MIN_SLOT <= slot <= MAX_SLOT else None


def _classify_recorded_gesture(
    *,
    duration_ms: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    moves: list[dict[str, Any]],
    force_drag: bool,
    click_max_duration_ms: int,
    click_max_move_px: int,
) -> str:
    if force_drag:
        return "drag"
    points = [(int(move["x"]), int(move["y"])) for move in moves]
    points.append((int(end_x), int(end_y)))
    max_excursion = max((math.hypot(x - start_x, y - start_y) for x, y in points), default=0.0)
    if duration_ms <= click_max_duration_ms and max_excursion <= click_max_move_px:
        return "click"
    return "drag"


def _call_stop_requested(stop_requested: Callable[[], bool] | None) -> bool:
    return bool(stop_requested and stop_requested())


def _activate_foreground_window(
    hwnd: int,
    *,
    user32: Any | None = None,
    timeout_sec: float = 1.0,
) -> None:
    api = user32 or _foreground_input_api()
    handle = wintypes.HWND(int(hwnd))
    api.ShowWindow(handle, SW_RESTORE)
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while True:
        api.BringWindowToTop(handle)
        api.SetForegroundWindow(handle)
        if int(api.GetForegroundWindow() or 0) == int(hwnd):
            time.sleep(0.15)
            return
        if time.monotonic() >= deadline:
            actual = int(api.GetForegroundWindow() or 0)
            raise RuntimeError(
                f"target window did not become foreground: expected=0x{int(hwnd):X} actual=0x{actual:X}"
            )
        time.sleep(0.05)


def _foreground_input_api():
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ULONG_PTR]
    user32.mouse_event.restype = None
    return user32


def _window_message_api():
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def _input_api():
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, kernel32


def _pump_messages(user32) -> None:
    msg = MSG()
    while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("PC script recording/playback requires Windows")
